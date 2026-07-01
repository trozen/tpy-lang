# Dropping a JoinHandle without join() or detach() is a runtime panic: the
# spawned thread's result/exception would otherwise silently vanish (Rust's /
# raw std::thread's join-or-detach contract).
from tpy.thread import spawn


class One:
    def run(self) -> int:
        return 1


def main() -> None:
    h = spawn[int, One](One())
    print("spawned")
    # h dropped here, unconsumed -> panic


main()
