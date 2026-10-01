# Dropping a JoinHandle without join() or detach() detaches the thread, as
# Rust's JoinHandle does: no panic, the thread runs on and its result is
# discarded.
from tpy.thread import spawn


class One:
    def run(self) -> int:
        return 1


def main() -> None:
    h = spawn(One())  # tpyc: ok -- dropped unconsumed below
    print("spawned")
    # h dropped here, unconsumed -> the thread is detached, not a panic


main()
